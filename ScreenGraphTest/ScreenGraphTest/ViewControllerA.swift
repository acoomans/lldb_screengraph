//
//  ViewControllerA.swift
//  ScreenGraphTest
//
//  Created by Arnaud Coomans on 4/16/19.
//  Copyright © 2019 Arnaud Coomans. All rights reserved.
//

import UIKit

class ViewControllerA: UIViewController {

    @IBAction func buttonBTapped(_ sender: Any) {
        let storyboard : UIStoryboard = UIStoryboard(name: "Main", bundle: nil)
        let viewController = storyboard.instantiateViewController(withIdentifier: "ViewControllerB")
        navigationController?.pushViewController(viewController, animated: true)
        
        event("button_b_tapped_event")
    }
    
    @IBAction func buttonCTapped(_ sender: Any) {
        let storyboard : UIStoryboard = UIStoryboard(name: "Main", bundle: nil)
        let viewController = storyboard.instantiateViewController(withIdentifier: "ViewControllerC")
        navigationController?.pushViewController(viewController, animated: true)
        
        event("button_c_tapped_event")
    }
    
}
